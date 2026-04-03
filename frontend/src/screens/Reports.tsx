"use client"

import type React from "react"
import { useState } from "react"
import api from "../services/api"
import toast from "react-hot-toast"
import { BarChart3, DollarSign, FileText, Calendar } from "lucide-react"

interface SalesReport {
  period: {
    start_date: string
    end_date: string
  }
  summary: {
    total_amount: number
    total_cash: number
    total_mpesa: number
    total_transactions: number
  }
  daily_breakdown: Array<{
    date: string
    total_amount: number
    total_cash: number
    total_mpesa: number
    count: number
  }>
}

interface SupplierReport {
  period: {
    start_date: string
    end_date: string
  }
  suppliers: Array<{
    supplier_name: string
    total_amount: number
    invoice_count: number
    invoices: Array<{
      invoice_number: string
      amount: number
      date: string
    }>
  }>
}

interface ShiftReport {
  period: {
    start_date: string
    end_date: string
  }
  shifts: Array<{
    shift_id: number
    cashier: string
    date: string
    expected_cash: number
    declared_cash: number
    cash_variance: number
    expected_mpesa: number
    declared_mpesa: number
    mpesa_variance: number
    notes: string
  }>
}

const Reports: React.FC = () => {
  const [activeTab, setActiveTab] = useState<"sales" | "suppliers" | "shifts">("sales")
  const [startDate, setStartDate] = useState("")
  const [endDate, setEndDate] = useState("")
  const [loading, setLoading] = useState(false)
  const [salesReport, setSalesReport] = useState<SalesReport | null>(null)
  const [supplierReport, setSupplierReport] = useState<SupplierReport | null>(null)
  const [shiftReport, setShiftReport] = useState<ShiftReport | null>(null)

  const generateSalesReport = async () => {
    if (!startDate || !endDate) {
      toast.error("Please select start and end dates")
      return
    }

    setLoading(true)
    try {
      const response = await api.get("/reports/sales/", {
        params: { start_date: startDate, end_date: endDate },
      })
      setSalesReport(response.data)
    } catch (error) {
      toast.error("Error generating sales report")
    } finally {
      setLoading(false)
    }
  }

  const generateSupplierReport = async () => {
    if (!startDate || !endDate) {
      toast.error("Please select start and end dates")
      return
    }

    setLoading(true)
    try {
      const response = await api.get("/reports/suppliers/", {
        params: { start_date: startDate, end_date: endDate },
      })
      setSupplierReport(response.data)
    } catch (error) {
      toast.error("Error generating supplier report")
    } finally {
      setLoading(false)
    }
  }

  const generateShiftReport = async () => {
    if (!startDate || !endDate) {
      toast.error("Please select start and end dates")
      return
    }

    setLoading(true)
    try {
      const response = await api.get("/reports/shifts/", {
        params: { start_date: startDate, end_date: endDate },
      })
      setShiftReport(response.data)
    } catch (error) {
      toast.error("Error generating shift report")
    } finally {
      setLoading(false)
    }
  }

  const formatCurrency = (amount: number) => {
    return new Intl.NumberFormat("en-KE", {
      style: "currency",
      currency: "KES",
    }).format(amount)
  }

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString("en-KE")
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Reports</h1>
      </div>

      {/* Tab Navigation */}
      <div className="border-b border-gray-200">
        <nav className="-mb-px flex space-x-8">
          <button
            onClick={() => setActiveTab("sales")}
            className={`py-2 px-1 border-b-2 font-medium text-sm ${
              activeTab === "sales"
                ? "border-blue-500 text-blue-600"
                : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
            }`}
          >
            <BarChart3 className="inline mr-2 h-4 w-4" />
            Sales Reports
          </button>
          <button
            onClick={() => setActiveTab("suppliers")}
            className={`py-2 px-1 border-b-2 font-medium text-sm ${
              activeTab === "suppliers"
                ? "border-blue-500 text-blue-600"
                : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
            }`}
          >
            <FileText className="inline mr-2 h-4 w-4" />
            Supplier Reports
          </button>
          <button
            onClick={() => setActiveTab("shifts")}
            className={`py-2 px-1 border-b-2 font-medium text-sm ${
              activeTab === "shifts"
                ? "border-blue-500 text-blue-600"
                : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
            }`}
          >
            <Calendar className="inline mr-2 h-4 w-4" />
            Shift Variance
          </button>
        </nav>
      </div>

      {/* Date Range Selector */}
      <div className="bg-white shadow rounded-lg p-6">
        <div className="flex items-center space-x-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Start Date</label>
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">End Date</label>
            <input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div className="pt-6">
            <button
              onClick={() => {
                if (activeTab === "sales") generateSalesReport()
                else if (activeTab === "suppliers") generateSupplierReport()
                else generateShiftReport()
              }}
              disabled={loading}
              className="bg-blue-600 text-white px-6 py-2 rounded-md hover:bg-blue-700 disabled:opacity-50"
            >
              {loading ? "Generating..." : "Generate Report"}
            </button>
          </div>
        </div>
      </div>

      {/* Sales Report */}
      {activeTab === "sales" && salesReport && (
        <div className="space-y-6">
          {/* Summary Cards */}
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
            <div className="bg-white overflow-hidden shadow rounded-lg">
              <div className="p-5">
                <div className="flex items-center">
                  <div className="flex-shrink-0">
                    <DollarSign className="h-6 w-6 text-gray-400" />
                  </div>
                  <div className="ml-5 w-0 flex-1">
                    <dl>
                      <dt className="text-sm font-medium text-gray-500 truncate">Total Sales</dt>
                      <dd className="text-lg font-medium text-gray-900">
                        {formatCurrency(salesReport.summary.total_amount)}
                      </dd>
                    </dl>
                  </div>
                </div>
              </div>
            </div>

            <div className="bg-white overflow-hidden shadow rounded-lg">
              <div className="p-5">
                <div className="flex items-center">
                  <div className="flex-shrink-0">
                    <div className="w-6 h-6 bg-green-500 rounded-full flex items-center justify-center">
                      <span className="text-white text-xs font-medium">C</span>
                    </div>
                  </div>
                  <div className="ml-5 w-0 flex-1">
                    <dl>
                      <dt className="text-sm font-medium text-gray-500 truncate">Cash Sales</dt>
                      <dd className="text-lg font-medium text-gray-900">
                        {formatCurrency(salesReport.summary.total_cash)}
                      </dd>
                    </dl>
                  </div>
                </div>
              </div>
            </div>

            <div className="bg-white overflow-hidden shadow rounded-lg">
              <div className="p-5">
                <div className="flex items-center">
                  <div className="flex-shrink-0">
                    <div className="w-6 h-6 bg-blue-500 rounded-full flex items-center justify-center">
                      <span className="text-white text-xs font-medium">M</span>
                    </div>
                  </div>
                  <div className="ml-5 w-0 flex-1">
                    <dl>
                      <dt className="text-sm font-medium text-gray-500 truncate">M-Pesa Sales</dt>
                      <dd className="text-lg font-medium text-gray-900">
                        {formatCurrency(salesReport.summary.total_mpesa)}
                      </dd>
                    </dl>
                  </div>
                </div>
              </div>
            </div>

            <div className="bg-white overflow-hidden shadow rounded-lg">
              <div className="p-5">
                <div className="flex items-center">
                  <div className="flex-shrink-0">
                    <BarChart3 className="h-6 w-6 text-gray-400" />
                  </div>
                  <div className="ml-5 w-0 flex-1">
                    <dl>
                      <dt className="text-sm font-medium text-gray-500 truncate">Transactions</dt>
                      <dd className="text-lg font-medium text-gray-900">{salesReport.summary.total_transactions}</dd>
                    </dl>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Daily Breakdown */}
          <div className="bg-white shadow rounded-lg">
            <div className="px-4 py-5 sm:p-6">
              <h3 className="text-lg leading-6 font-medium text-gray-900 mb-4">Daily Breakdown</h3>
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Date
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Total Sales
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Cash
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        M-Pesa
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        Transactions
                      </th>
                    </tr>
                  </thead>
                  <tbody className="bg-white divide-y divide-gray-200">
                    {salesReport.daily_breakdown.map((day) => (
                      <tr key={day.date}>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">{formatDate(day.date)}</td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                          {formatCurrency(day.total_amount)}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                          {formatCurrency(day.total_cash)}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                          {formatCurrency(day.total_mpesa)}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">{day.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Supplier Report */}
      {activeTab === "suppliers" && supplierReport && (
        <div className="bg-white shadow rounded-lg">
          <div className="px-4 py-5 sm:p-6">
            <h3 className="text-lg leading-6 font-medium text-gray-900 mb-4">Supplier Invoice Report</h3>
            <div className="space-y-6">
              {supplierReport.suppliers.map((supplier) => (
                <div key={supplier.supplier_name} className="border border-gray-200 rounded-lg p-4">
                  <div className="flex justify-between items-center mb-4">
                    <h4 className="text-lg font-medium text-gray-900">{supplier.supplier_name}</h4>
                    <div className="text-right">
                      <p className="text-sm text-gray-500">{supplier.invoice_count} invoices</p>
                      <p className="text-lg font-bold text-gray-900">{formatCurrency(supplier.total_amount)}</p>
                    </div>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-200">
                      <thead className="bg-gray-50">
                        <tr>
                          <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                            Invoice Number
                          </th>
                          <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Date</th>
                          <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Amount</th>
                        </tr>
                      </thead>
                      <tbody className="bg-white divide-y divide-gray-200">
                        {supplier.invoices.map((invoice) => (
                          <tr key={invoice.invoice_number}>
                            <td className="px-4 py-2 text-sm text-gray-900">{invoice.invoice_number}</td>
                            <td className="px-4 py-2 text-sm text-gray-900">{formatDate(invoice.date)}</td>
                            <td className="px-4 py-2 text-sm text-gray-900">{formatCurrency(invoice.amount)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Shift Variance Report */}
      {activeTab === "shifts" && shiftReport && (
        <div className="bg-white shadow rounded-lg">
          <div className="px-4 py-5 sm:p-6">
            <h3 className="text-lg leading-6 font-medium text-gray-900 mb-4">Shift Variance Report</h3>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Date
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Cashier
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Expected Cash
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Declared Cash
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Cash Variance
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Expected M-Pesa
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Declared M-Pesa
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      M-Pesa Variance
                    </th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {shiftReport.shifts.map((shift) => (
                    <tr
                      key={shift.shift_id}
                      className={
                        Math.abs(shift.cash_variance) > 0 || Math.abs(shift.mpesa_variance) > 0 ? "bg-red-50" : ""
                      }
                    >
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">{formatDate(shift.date)}</td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">{shift.cashier}</td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                        {formatCurrency(shift.expected_cash)}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                        {formatCurrency(shift.declared_cash)}
                      </td>
                      <td
                        className={`px-6 py-4 whitespace-nowrap text-sm font-medium ${
                          shift.cash_variance > 0
                            ? "text-green-600"
                            : shift.cash_variance < 0
                              ? "text-red-600"
                              : "text-gray-900"
                        }`}
                      >
                        {formatCurrency(shift.cash_variance)}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                        {formatCurrency(shift.expected_mpesa)}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                        {formatCurrency(shift.declared_mpesa)}
                      </td>
                      <td
                        className={`px-6 py-4 whitespace-nowrap text-sm font-medium ${
                          shift.mpesa_variance > 0
                            ? "text-green-600"
                            : shift.mpesa_variance < 0
                              ? "text-red-600"
                              : "text-gray-900"
                        }`}
                      >
                        {formatCurrency(shift.mpesa_variance)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default Reports
